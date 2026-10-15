#!/usr/bin/env python3
"""Measure actual Native scanout dotclock event delivery into the CPU domain.

Diagnostic only: baseline loss is a reported failure, never a selected cut or
replacement. --require-lossless makes that result an explicit failing gate.
No BIOS/disc, production RTL/clock changes, analog metastability model or game
claim. A same-frequency observer-clock control validates event accounting.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
from ghdl_verilog import normalize_generated_verilog
from measure_pocket_seeds import inputs
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest,hardware_text
from prepare_video_reports_cdc import EDGE,STROBE
from tool_paths import ROOT as R,find_tool


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('project',type=Path)
    p.add_argument('--expected-video-sha256',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--require-lossless',action='store_true')
    p.add_argument('--video-cycles',type=int,default=100000)
    p.add_argument('--pal',action='store_true')
    p.add_argument('--interlace',action='store_true')
    p.add_argument('--color24',action='store_true')
    args=p.parse_args();D=args.project.resolve();W=args.output.resolve()
    if not 100000<=args.video_cycles<=5000000:raise ValueError('Require 100000..5000000 video cycles per mode')
    if W.exists():raise ValueError('Fresh census output required')
    W.mkdir(parents=True);sources=inputs(D)
    def byname(name):
        matches=[Path(s['path']) for s in sources if Path(s['path']).name==name]
        if len(matches)!=1:raise ValueError('Expected one assigned source: '+name)
        return matches[0]
    # Use the exact QSF-selected Native engine, not its earlier copied source.
    selected=[p for k,p in assignments(D,(D/'apf_top.qsf').read_text()) if k=='VHDL_FILE' and p.name=='gpu_videoout_async.vhd']
    if len(selected)!=1:raise ValueError('Expected exactly one assigned scanout engine')
    selected=selected[0]
    if digest(selected)!=args.expected_video_sha256:raise ValueError('Selected video source identity changed')
    timer=byname('timer.vhd');timer_text=timer.read_text()
    if timer_text.count(EDGE)==1 and STROBE not in timer_text:strobe_timer=False
    elif timer_text.count(STROBE)==1 and EDGE not in timer_text:strobe_timer=True
    else:raise ValueError('Unknown compiled timer dotclock consumer')
    report_sources=[p for kind,p in assignments(D,(D/'apf_top.qsf').read_text()) if p.name=='psx_video_reports_cdc.sv']
    if len(report_sources)!=int(strobe_timer):raise ValueError('Report transport and timer encoding are not paired')
    core=byname('core_top.sv')
    for frequency in ('33.868800','53.693166'):
        if core.read_text().count('.output_clock_frequency0("'+frequency+' MHz")')!=1:
            raise ValueError('Unknown Native CPU/video clock configuration')
    record=[];protected={str(core):digest(core)}
    (W/'inputs').mkdir()
    for name,p in [('pGPU.vhd',byname('pGPU.vhd')),('gpu_dither.vhd',byname('gpu_dither.vhd')),('gpu_videoout_async.vhd',selected),('timer.vhd',timer)]:
        q=W/'inputs'/name;shutil.copyfile(p,q);shutil.copyfile(q,W/name);protected[str(p)]=digest(p);record.append(dict(original=str(p),path=str(q),sha256=digest(q)))
    extra=['psx_video_reports_cdc.sv'] if strobe_timer else []
    for name in ['psx_video_request_cdc.sv','psx_video_config_cdc.sv','dual_clock_fifo.sv',*extra]:
        p=byname(name);q=W/'inputs'/name;shutil.copyfile(p,q);protected[str(p)]=digest(p)
        record.append(dict(original=str(p),path=str(q),sha256=digest(q)))
    model=R/'references/PSX_MiSTer/sim/system/src/mem/dpram.vhd'
    raw_ram=W/'inputs/dpram.vhd';shutil.copyfile(model,raw_ram);protected[str(model)]=digest(model)
    record.append(dict(original=str(model),path=str(raw_ram),sha256=digest(raw_ram)))
    stub=W/'stub.vhd';stub.write_text('''library ieee;use ieee.std_logic_1164.all;
 entity psx_video_config_cdc is port(src_clk,dst_clk,reset_async,src_valid:in std_logic;
 src_data:in std_logic_vector(83 downto 0);src_ready:out std_logic;dst_data:out std_logic_vector(83 downto 0);
 dst_update,dst_initialized,src_fault:out std_logic);end entity;
 architecture opaque of psx_video_config_cdc is begin end architecture;
 library ieee;use ieee.std_logic_1164.all;
 entity psx_video_request_cdc is port(src_clk,dst_clk,reset_async:in std_logic;
 src_data:in std_logic_vector(38 downto 0);src_ready,src_fault:out std_logic;dst_data:out std_logic_vector(38 downto 0);
 dst_initialized,dst_fault:out std_logic);end entity;
 architecture opaque of psx_video_request_cdc is begin end architecture;
 library ieee;use ieee.std_logic_1164.all;
 entity psx_video_reports_cdc is port(src_clk,dst_clk,reset_async:in std_logic;
 src_levels:in std_logic_vector(8 downto 0);src_dot:in std_logic;
 src_ready,src_fault:out std_logic;dst_levels:out std_logic_vector(8 downto 0);
 dst_dot,dst_update,dst_initialized,dst_fault:out std_logic);end entity;
 architecture opaque of psx_video_reports_cdc is begin end architecture;
 ''')
    ram=W/'ram.vhd';ram.write_text(re.sub(r'\bdpram\b','pocket_dpram_sdp',raw_ram.read_text(),flags=re.I).split('--------------------------------------------------------------')[0])
    dummy=W/'dummy.vhd';dummy.write_text('package dummy is end package;')
    p=W/'gpu_videoout_async.vhd';s=p.read_text();marker='architecture arch of gpu_videoout_async is';binding='\n   for video_config:psx_video_config_cdc use entity videoip.psx_video_config_cdc;\n   for video_request:psx_video_request_cdc use entity videoip.psx_video_request_cdc;\n'
    if strobe_timer:binding+='   for video_reports:psx_video_reports_cdc use entity videoip.psx_video_reports_cdc;\n'
    assert s.count(marker)==1;s=re.sub(r'^\s*attribute[^\n]*\n','',hardware_text(s),flags=re.M)
    for value in ('lineMax - 1','lineMax - 2','((vdispNew - vDisplayStart) * 2) + 1','(vdispNew - vDisplayStart) * 2','vdispNew - vDisplayStart'):
        s=s.replace('to_unsigned('+value+', 9)','resize(unsigned(to_signed('+value+', 12)), 9)')
    p.write_text('library videoip;\n'+s.replace(marker,marker+binding))
    with (W/'build.log').open('w') as log:
        for lib,p in [('mem',dummy),('videoip',stub),('work',W/'pGPU.vhd'),('work',ram),('work',W/'gpu_dither.vhd'),('work',W/'gpu_videoout_async.vhd')]:
            subprocess.run([find_tool('ghdl'),'-a','--std=08','-frelaxed','--work='+lib,str(p)],cwd=W,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
        with (W/'scanout.v').open('w') as target:
            subprocess.run([find_tool('ghdl'),'--synth','--std=08','-frelaxed','--vendor-library=videoip','--out=verilog','gpu_videoout_async'],cwd=W,stdout=target,stderr=log,check=True,timeout=120)
    normalize_generated_verilog(W/'scanout.v')
    (W/'source.json').write_text(json.dumps(dict(sources=record,adaptation='External SV component bindings; portable dither RAM; inference attributes stripped for GHDL bug; low9bit blank/reset offset conversions; report/dotclock logic unchanged',converted_sha256=digest(W/'scanout.v')),indent=2)+'\n')


    s=(W/'scanout.v').read_text();h=s[s.index('module gpu_videoout_async\n'):].split(');',1)[0]
    ports=re.findall(r'\b(input|output)\s+(\[[^\]]+\]\s+)?(\\[^\s,]+|[A-Za-z_]\w*)',h)
    clean=lambda n:re.sub(r'\W','_',n.lstrip('\\'))
    name=lambda field:'i_'+clean('videoout_settings_1x['+field+']')
    text='`timescale 1ns/1ps\nmodule tb;\n'
    for direction,width,n in ports:text+=('reg' if direction=='input' else 'wire')+' '+width+('i_' if direction=='input' else 'o_')+clean(n)+('=0' if direction=='input' else '')+';\n'
    text+='''integer phase=0;reg run_video=1;reg window=0;integer sent=0,received=0;reg last=0;
 real cpu_half=14.76255;
 initial begin if(!$value$plusargs("cpu_half=%f",cpu_half))cpu_half=14.76255;
 forever #(cpu_half)i_clk1x=~i_clk1x;end
 always #7.381275 i_clk2x=~i_clk2x;
 initial begin if(!$value$plusargs("phase=%d",phase))phase=0;
 #(phase/1000.0);forever begin #9.31219; if(run_video)i_clkvid=~i_clkvid;else i_clkvid=0;end end
 '''
    ass=[]
    for direction,width,n in ports:ass.append('.'+n+(' ' if n.startswith('\\') else '')+'('+('i_' if direction=='input' else 'o_')+clean(n)+')')
    text+='gpu_videoout_async dut ('+',\n'.join(ass)+');\n'
    src='o_'+clean('videoout_out[ce]');dst='o_'+clean('videoout_reports_1x[dotclock]')
    # Drain the new packet FIFO completely, including its delayed read strobe.
    # Keep this below the forward config FIFO's stopped-reader capacity.
    drain_cycles=9 if strobe_timer else 6
    drained="!dut.video_reports.snapshots.empty_o || dut.video_reports.read_valid || dut.video_reports.snapshots.wr_addr!=dut.video_reports.snapshots.rd_addr" if strobe_timer else 'last'
    text+=f'''always @(negedge i_clkvid)if(window && {src})sent++;
    always @(posedge i_clk1x)begin
        if(window && {dst} && {"1'b1" if strobe_timer else "!last"})received++;
        last<={dst};
    end
    initial begin i_ce_1x=1;
    '''
    for field,val in [('hDisplayRange',"{12'd3168,12'd608}"),('vDisplayRange',"{10'd256,10'd16}"),('vramRange',"19'h2043")]:text+=name(field)+'='+val+';\n'
    for field,value in [('GPUSTAT_PalVideoMode',args.pal),('GPUSTAT_VerRes',args.interlace),
            ('GPUSTAT_VertInterlace',args.interlace),('GPUSTAT_ColorDepth24',args.color24)]:
        text+=name(field)+'='+str(int(value))+';\n'
    text+=f'''for(integer res=0;res<5;res++)begin
        run_video=1;i_reset_1x=1;
        {name('GPUSTAT_HorRes1')}=2'(res);{name('GPUSTAT_HorRes2')}=(res==4);
        repeat(80)@(negedge i_clk1x);i_reset_1x=0;
        repeat(1000)@(negedge i_clkvid);

        while({src})@(negedge i_clkvid);
        run_video=0;repeat({drain_cycles})@(negedge i_clk1x);
        if({dst}||({drained}))$fatal(1,"Initial pipeline failed to drain source=%b report=%b last=%b",{src},{dst},last);
        sent=0;received=0;window=1;run_video=1;
        repeat({args.video_cycles})@(negedge i_clkvid);
        while({src})@(negedge i_clkvid);
        run_video=0;repeat({drain_cycles})@(negedge i_clk1x);window=0;
        if({dst}||({drained}))$fatal(1,"Final pipeline failed to drain");
        if(sent<({args.video_cycles}*9)/100||received==0||received>sent)$fatal(1,"Bad accounting sent=%0d received=%0d",sent,received);
        $display("DOTCLOCK phase=%0d mode=%0d sent=%0d received=%0d missing=%0d",phase,res,sent,received,sent-received);
    end
    $finish;end
    initial begin #{int(args.video_cycles*5*18.62438+3000000)};$fatal(1,"Timeout");end
    endmodule
    '''
    bench=W/'tb.sv';bench.write_text(text)
    sources=[W/'scanout.v',W/'inputs/psx_video_request_cdc.sv',W/'inputs/psx_video_config_cdc.sv',W/'inputs/dual_clock_fifo.sv',bench]
    sources += [W/'inputs'/name for name in extra]
    with (W/'compile.log').open('w') as log:subprocess.run([find_tool('verilator'),'--binary','--timing','-j','2','-Wno-fatal','--top-module','tb','--Mdir',str(W/'obj'),'-o','sim',*[str(p) for p in sources]],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180)
    # Native periods in ns (rounded by the simulator to 1ps), four phase offsets.
    # Equal-frequency observer is a test control, never a hardware clock option.
    runs=[]
    for phase,cpu_half in [(0,14.76255),(1700,14.76255),(5300,14.76255),(11000,14.76255),(5300,9.31219)]:
        run=subprocess.run([str(W/'obj/sim'),'+phase='+str(phase),'+cpu_half='+str(cpu_half)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=120)
        (W/f'phase{phase}-cpu{cpu_half}.log').write_text(run.stdout)
        assert run.returncode==0,run.stdout
        found=[dict(zip(['phase','mode','sent','received','missing'],map(int,m))) for m in re.findall(r'DOTCLOCK phase=(\d+) mode=(\d+) sent=(\d+) received=(\d+) missing=(\d+)',run.stdout)]
        assert len(found)==5 and {row['mode'] for row in found}==set(range(5))

        if cpu_half==9.31219:assert all(r['missing']==0 for r in found),found
        for row in found:row['cpu_half_ns']=cpu_half;row['equal_frequency_control']=cpu_half==9.31219
        runs.extend(found);print(run.stdout,flush=True)
    native_lossless=all(row['missing']==0 for row in runs if not row['equal_frequency_control'])
    assert all(digest(Path(p))==sha for p,sha in protected.items())
    assert all(digest(Path(row['path']))==row['sha256'] for row in record)
    report=dict(native_lossless=native_lossless,protected_inputs_unchanged=True,
        protected_sha256=protected,script_sha256=digest(Path(__file__)),
        scope='Exact selected Native scanout after GHDL conversion; source pulses vs compiled timer input tick rule; CE held1, drain at window boundaries',
        timer_input_rule='strobe' if strobe_timer else 'rising-edge',
        sources={str(p):digest(p) for p in sources},source=json.loads((W/'source.json').read_text()),
        simulated_nominal_period_ns=dict(cpu=29.5251,video=18.62438,precision=.001),
        video_cycles_per_mode=args.video_cycles,pal=args.pal,interlace=args.interlace,color24=args.color24,
        runs=runs,ideal_digital_only=True,analog_metastability_covered=False,hardware_or_game_test=False)
    (W/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(W/'report.json', 'NATIVE_LOSSLESS='+str(native_lossless),flush=True)
    if args.require_lossless and not native_lossless:raise SystemExit('Native dotclock transport loses events')


if __name__=='__main__':main()
