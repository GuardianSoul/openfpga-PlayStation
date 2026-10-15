#!/usr/bin/env python3
"""Compare ordered video packets with pinned scanout; no BIOS/game image.

Checks2048rapid settings/control changes at each of two source phases,11steady display modes, first-active
size readiness and every event-aligned video/read-address/fetch sample in two
frames per mode. Includes an actual active-red RTL corruption control. Added
packet/startup delay remains explicit; this is not cycle/game equivalence.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from prepare_resource_targets import digest
from prepare_video_config_cdc import FIELDS
from prepare_video_config_sim import prepare
from tool_paths import ROOT,find_tool


def main():
    root=ROOT
    parent=ROOT/'build/sim/video-config-pipeline';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    converted=work/'converted';prepared=prepare(converted)
    out=work/'integration';out.mkdir()
    source=(converted/'original.v').read_text();header=source[source.index('module original__video_original'):].split(');',1)[0]
    ports=re.findall(r'\b(input|output)\s+(\[[^\]]+\]\s+)?(\\[^\s,]+|[A-Za-z_]\w*)',header)
    def clean(n):return re.sub(r'\W','_',n.lstrip('\\'))
    def inp(name):return 'i_'+clean('videoout_settings_1x['+name+']')
    text='`timescale 1ns/1ps\nmodule tb_video;\n'
    special={'clk1x','clk2x','clkvid','videoout_pixelRead','videoout_pixelRead2'}
    for direction,width,name in ports:
        short=name.lstrip('\\')
        if direction=='input' and short not in special:
            init=1 if short in ('ce_1x','reset_1x') else 0
            text+=f'reg {width}i_{clean(name)}={init};\n'
        elif direction=='output':
            for p in ('old','new'):text+=f'wire {width}{p}_{clean(name)};\n'
    text+='reg clk1x=0,clk2x=0,clkvid=0;\nalways #14.76255 clk1x=~clk1x;\nalways #7.381275 clk2x=~clk2x;\nalways #9.31219 clkvid=~clkvid;\n'
    for label,module in [('old','original__video_original'),('new','candidate__video_candidate')]:
        text+=f'reg [15:0] {label}_pixel=0,{label}_pixel2=0;\n'
        text+=f'always @(posedge clkvid) begin {label}_pixel<=16\'(({label}_videoout_readAddr*73)^16\'h639c);{label}_pixel2<=16\'((({label}_videoout_readAddr+1)*73)^16\'h639c);end\n'
        connections=[]
        for direction,width,name in ports:
            short=name.lstrip('\\')
            if direction=='output':value=label+'_'+clean(name)
            elif short in ('clk1x','clk2x','clkvid'):value=short
            elif short=='videoout_pixelRead':value=label+'_pixel'
            elif short=='videoout_pixelRead2':value=label+'_pixel2'
            else:value='i_'+clean(name)
            connections.append('.'+name+(' ' if name.startswith('\\') else '')+'('+value+')')
        text+=module+' '+label+'_dut ('+',\n'.join(connections)+');\n'
    text+='wire [79:0] expected_native={'+','.join(inp(n) for n,w,k in reversed(FIELDS))+'};\n'
    text+='wire [79:0] expected_fifo={'+','.join(inp(n) for n,w,k in FIELDS)+'};\n'
    text+='''wire [83:0] expected_packet={i_ce_1x,i_softReset_1x,i_savestate_pause_1x,i_system_paused_1x,expected_native};
    wire [83:0] expected_input={i_ce_1x,i_softReset_1x,i_savestate_pause_1x,i_system_paused_1x,expected_fifo};
    reg [83:0] queue[0:63];integer sent=0,received=0,checked=0,abandoned=0;
    always @(posedge new_dut.video_config_reset) begin abandoned+=sent-received;sent=0;received=0;end
    always @(posedge clk1x) if(!new_dut.video_config_reset && new_dut.video_config.src_valid && new_dut.video_config.src_ready) begin
     if(new_dut.video_config.src_data!==expected_input) $fatal(1,"VHDL source packing mismatch");
     queue[sent&63]=expected_packet;sent++;
     if(sent-received>=64)$fatal(1,"Integration queue wrapped");
    end
    always @(negedge clkvid) begin
     if(!new_dut.video_config_reset && new_dut.video_config.dst_update) begin
      if(received>=sent || {new_dut.ce,new_dut.softreset,new_dut.savestate_pause,new_dut.system_paused,new_dut.videoout_settings}!==queue[received&63]) $fatal(1,"VHDL settings/control unpack/order mismatch");
      received++;checked++;
     end
     if(new_dut.video_config.src_fault)$fatal(1,"Video clock integration overflow");
     if(old_dut.reset_s1!==new_dut.reset_s1) $fatal(1,"Reset sampling schedule changed");
    end
    integer frame[0:1],done_frames[0:1],pixels[0:1],ticks[0:1];
    reg vs_old[0:1];reg [63:0] hash[0:1],result_hash[0:1][0:1];
    integer result_pixels[0:1][0:1],result_ticks[0:1][0:1];
    reg collecting=0;
    reg [127:0] samples[0:1][0:1][0:1099999];
    function automatic [63:0] mix(input [63:0] h,input [63:0] value);mix=(h^value)*64'h100000001b3;endfunction
    reg measure_transport=0;realtime born[bit[79:0]],old_arrived[bit[79:0]];
    reg [79:0] last_old=0,last_new=0;integer old_changes=0,new_changes=0;
    real old_min=1e20,old_max=0,new_min=1e20,new_max=0,delta_min=1e20,delta_max=0,elapsed,extra;
    always @(negedge clkvid) if(measure_transport) begin
     if(old_dut.videoout_settings!==last_old && born.exists(old_dut.videoout_settings)) begin
      elapsed=$realtime-born[old_dut.videoout_settings];old_changes++;
      if(elapsed<old_min)old_min=elapsed;if(elapsed>old_max)old_max=elapsed;
      old_arrived[old_dut.videoout_settings]=$realtime;
     end
     if(new_dut.videoout_settings!==last_new && born.exists(new_dut.videoout_settings)) begin
      elapsed=$realtime-born[new_dut.videoout_settings];new_changes++;
      if(elapsed<new_min)new_min=elapsed;if(elapsed>new_max)new_max=elapsed;
      if(!old_arrived.exists(new_dut.videoout_settings))$fatal(1,"Reference missed a configuration change");
      extra=$realtime-old_arrived[new_dut.videoout_settings];
      if(extra<delta_min)delta_min=extra;if(extra>delta_max)delta_max=extra;
     end
     last_old=old_dut.videoout_settings;last_new=new_dut.videoout_settings;
    end
    '''
    for idx,label in enumerate(('old','new')):
        def signal(s):return label+'_'+clean('videoout_out['+s+']')
        names=[signal(s) for s in ('r','g','b','ce','hblank','vblank','hsync','vsync','interlace','DisplayWidthReal','DisplayHeightReal')]
        names+=[label+'_videoout_readAddr']+[label+'_'+clean('videoout_request_vid['+s+']') for s in ('fetch','fetchsize','lineInNext','xpos','lineDisp')]
        payload='{'+','.join(names)+'}'
        text+=f'''always @(negedge clkvid) if(collecting) begin
     if({signal('vsync')} && !vs_old[{idx}]) begin
      if(frame[{idx}]>=3 && done_frames[{idx}]<2) begin
       result_hash[{idx}][done_frames[{idx}]]=hash[{idx}];
       result_pixels[{idx}][done_frames[{idx}]]=pixels[{idx}];result_ticks[{idx}][done_frames[{idx}]]=ticks[{idx}];done_frames[{idx}]++;
      end
      frame[{idx}]++;hash[{idx}]=0;pixels[{idx}]=0;ticks[{idx}]=0;
     end
     vs_old[{idx}]={signal('vsync')};
     if(frame[{idx}]>=3 && frame[{idx}]<5) begin
      if(ticks[{idx}]>=1100000)$fatal(1,"Video frame exceeded trace capacity");
      samples[{idx}][frame[{idx}]-3][ticks[{idx}]]=128'({payload});
     end
     hash[{idx}]=mix(hash[{idx}],64'({payload}));ticks[{idx}]++;
     if({signal('ce')} && !{signal('hblank')} && !{signal('vblank')}) pixels[{idx}]++;
    end
    '''
    text+='''reg check_startup=0,previous_video_reset=1;integer startup_checks=0;
    always @(negedge clkvid) begin
     if(check_startup && previous_video_reset && !new_dut.reset) begin
      if(new_dut.vdisplaystart!==i_videoout_settings_1x_vDisplayRange_[9:0] ||
         new_dut.vdisplayend!==i_videoout_settings_1x_vDisplayRange_[19:10] ||
         new_dut.linemax!=(int'(i_videoout_settings_1x_vDisplayRange_[19:10])-int'(i_videoout_settings_1x_vDisplayRange_[9:0]))*(i_videoout_settings_1x_GPUSTAT_VerRes_ ? 2:1))
        $fatal(1,"Derived video size was not ready at first active clock start=%0d end=%0d lines=%0d",new_dut.vdisplaystart,new_dut.vdisplayend,new_dut.linemax);
      startup_checks++;
     end
     previous_video_reset=new_dut.reset;
    end
    integer modes=0;
    task run_mode(input integer res,input bit pal,input bit interlace,input bit color24,input bit full24=0,input bit dith=0);
     begin
      collecting=0;check_startup=1;
      @(negedge clk1x);i_reset_1x=1;
      i_ce_1x=1;i_softReset_1x=0;i_savestate_pause_1x=0;i_system_paused_1x=0;
    '''
    for name,value in [('GPUSTAT_VerRes','interlace'),('GPUSTAT_VertInterlace','interlace'),('GPUSTAT_PalVideoMode','pal'),
     ('GPUSTAT_HorRes2',"res==4"),('GPUSTAT_HorRes1',"2'(res)"),('GPUSTAT_ColorDepth24','color24'),
     ('GPUSTAT_DisplayDisable',"0"),('vramRange',"19'h2043"),('hDisplayRange',"{12'd3168,12'd608}"),
     ('vDisplayRange',"pal ? {10'd288,10'd48}:{10'd256,10'd16}"),('vCrop','0'),('hCrop','0'),('dither24','dith'),('render24','full24'),
     ('pal60','0'),('syncInterlace','0'),('rotate180','0'),('fixedVBlank','0')]:
        text+=f'  {inp(name)}={value};\n'
    text+='''  repeat(40) @(negedge clk1x);i_reset_1x=0;
      repeat(32) @(negedge clk1x);
      for(integer i=0;i<2;i++) begin frame[i]=0;done_frames[i]=0;hash[i]=0;pixels[i]=0;ticks[i]=0;vs_old[i]=0;end
      collecting=1;wait(done_frames[0]==2 && done_frames[1]==2);collecting=0;
      for(integer f=0;f<2;f++) begin
       if(result_hash[0][f]!==result_hash[1][f] || result_pixels[0][f]!=result_pixels[1][f] || result_ticks[0][f]!=result_ticks[1][f])
        $fatal(1,"Aligned video frame mismatch mode=%0d pal=%0d interlace=%0d color24=%0d frame=%0d hash=%h/%h pixels=%0d/%0d ticks=%0d/%0d",res,pal,interlace,color24,f,result_hash[0][f],result_hash[1][f],result_pixels[0][f],result_pixels[1][f],result_ticks[0][f],result_ticks[1][f]);
       if(result_pixels[0][f]==0)$fatal(1,"Empty frame cannot validate video");
       for(integer tick=0;tick<result_ticks[0][f];tick++)
        if(samples[0][f][tick]!==samples[1][f][tick])$fatal(1,"Exact aligned video/request/read-address sample mismatch frame=%0d tick=%0d got=%h expected=%h",f,tick,samples[1][f][tick],samples[0][f][tick]);
      end
      $display("VIDEO_MODE res=%0d pal=%0d interlace=%0d color24=%0d pixels=%0d,%0d ticks=%0d,%0d",res,pal,interlace,color24,result_pixels[0][0],result_pixels[0][1],result_ticks[0][0],result_ticks[0][1]);modes++;
     end
    endtask
    initial begin
     repeat(20) @(negedge clk1x);
     i_reset_1x=0;repeat(32) @(negedge clk1x);
     for(integer phase=0;phase<2;phase++) begin
      born.delete();old_arrived.delete();old_changes=0;new_changes=0;
      old_min=1e20;old_max=0;new_min=1e20;new_max=0;delta_min=1e20;delta_max=0;
      last_old=old_dut.videoout_settings;last_new=new_dut.videoout_settings;
      measure_transport=1;
      for(integer n=0;n<2048;n++) begin
       if(phase==0) @(negedge clk1x);
       else begin @(posedge clk1x);#0.001;end
      i_ce_1x=1'(n>>3);i_softReset_1x=(n%17)==0;i_savestate_pause_1x=1'(n>>5);i_system_paused_1x=1'(n>>6);
    '''
    rapid={'GPUSTAT_VerRes':"1'(n)",'GPUSTAT_PalVideoMode':"1'(n>>1)",'GPUSTAT_VertInterlace':"1'(n>>2)",
     'GPUSTAT_HorRes2':"1'(n>>3)",'GPUSTAT_HorRes1':"2'(n>>4)",'GPUSTAT_ColorDepth24':"1'(n>>6)",
     'GPUSTAT_DisplayDisable':"1'(n>>7)",'vramRange':"19'((n*271)^19'h1a049)",
     'hDisplayRange':"{12'(3000+n%64),12'(600+n%48)}",'vDisplayRange':"{10'(256+n%32),10'(16+n%32)}",
     'pal60':"1'(n>>8)",'syncInterlace':"1'(n>>9)",'rotate180':"1'(n>>10)",'fixedVBlank':"1'(n>>6)",
     'vCrop':"2'(n>>3)",'hCrop':"1'(n>>4)",'dither24':"1'(n>>5)",'render24':"1'(n>>7)"}
    for name,width,kind in FIELDS:text+=f'  {inp(name)}={rapid[name]};\n'
    text+='''  #0.001;born[expected_native]=$realtime;
     end
     repeat(32) @(negedge clk1x);measure_transport=0;
     if(old_changes!=2048 || new_changes!=2048)$fatal(1,"Configuration transition coverage missing %0d/%0d",old_changes,new_changes);
     $display("VIDEO_TRANSPORT phase=%0d changes=%0d/%0d reference_ns=%.3f..%.3f candidate_ns=%.3f..%.3f added_ns=%.3f..%.3f",phase,old_changes,new_changes,old_min,old_max,new_min,new_max,delta_min,delta_max);
     end
     run_mode(0,0,0,0);run_mode(1,0,0,0);run_mode(2,0,0,0);run_mode(3,0,0,0);run_mode(4,0,0,0);
     run_mode(1,1,0,0);run_mode(1,0,1,0);run_mode(1,0,0,1);
     run_mode(1,0,0,1,1,0);run_mode(1,0,0,1,0,1);run_mode(1,1,1,0);
     if(startup_checks!=11)$fatal(1,"Startup coverage missing");
     $display("VIDEO_STARTUP checks=%0d",startup_checks);
     $display("PASS VIDEO_INTEGRATION modes=%0d unpack_checks=%0d abandoned_on_reset=%0d",modes,checked,abandoned);$finish;
    end
    initial begin #1000000000;$fatal(1,"Video integration timeout");end
    endmodule
    '''
    bench=out/'tb.sv';bench.write_text(text)
    sources=[Path(prepared['original']),Path(prepared['candidate']),Path(prepared['memory_models']),
     Path(prepared['wrapper']),Path(prepared['fifo']),bench]
    protected=dict(prepared['source_sha256']);archives=dict(prepared['input_archives']);frozen=[]
    for i,p in enumerate(sources):
        sha=digest(p);copy=out/'input-archive'/str(i)/p.name;copy.parent.mkdir(parents=True)
        shutil.copyfile(p,copy)
        if digest(copy)!=sha:raise ValueError('Video pipeline source changed during capture')
        protected[str(p)]=sha;protected[str(copy)]=sha;archives[str(p)]=dict(path=str(copy),sha256=sha);frozen.append(copy)
    sources=frozen
    for p in (Path(__file__).resolve(),ROOT/'scripts/prepare_video_config_sim.py',ROOT/'scripts/prepare_video_config_cdc.py'):
        sha=digest(p);copy=out/'driver-archive'/p.name;copy.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,copy)
        if digest(copy)!=sha:raise ValueError('Video driver changed during capture')
        protected[str(p)]=sha;protected[str(copy)]=sha;archives[str(p)]=dict(path=str(copy),sha256=sha)
    with (out/'build.log').open('w') as f:
        subprocess.run([find_tool('verilator'),'--binary','--timing','-O3','-j','4','-Wno-fatal','--top-module','tb_video',
            '--Mdir',str(out/'obj'),*map(str,sources)],cwd=out,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)
    with (out/'run.log').open('w') as f:r=subprocess.run([str(out/'obj/Vtb_video')],cwd=out,stdout=f,stderr=subprocess.STDOUT,timeout=600)
    output=(out/'run.log').read_text();identity=all(digest(Path(p))==h for p,h in protected.items())
    passed=r.returncode==0 and 'PASS VIDEO_INTEGRATION modes=11' in output and identity
    (out/'report.json').write_text(json.dumps(dict(passed=passed,source_identity_verified=identity,source_sha256=protected,
     input_archives=archives,exit_code=r.returncode,output=output,log_sha256=digest(out/'run.log'),hardware_verified=False,
     cycle_equivalent=False,scope='Exact aligned video/request/read-address samples, fixed-mode frames,2048ordered varying-field settings at each of negedge and just-after-posedge input phases and measured added delivery latency; not transition-frame or game timing equivalence'),indent=2)+'\n')
    print(output,flush=True)
    if not passed:raise SystemExit(1)
    # Corrupt only active red pixels in an RTL copy. The frame hash truncates
    # RGB from its wide tuple; the exact128-bit sample oracle must catch this.
    fault=work/'fault-render';fault.mkdir()
    text=sources[1].read_text()
    def port_wire(field):
        m=re.search(r'assign \\videoout_out\['+field+r'\]\s*=\s*(\w+);',text)
        if not m:raise ValueError('GHDL video output fault anchor changed: '+field)
        return m.group(1)
    rgb,hblank,vblank,pixel_ce=[port_wire(f) for f in ('r','hblank','vblank','ce')]
    old='assign '+chr(92)+'videoout_out[r]  = '+rgb+';'
    if text.count(old)!=1:raise ValueError('Ambiguous red output assignment')
    changed=text.replace(old,old[:-1]+" ^ ((!"+hblank+" && !"+vblank+" && "+pixel_ce+") ? 8'h01:8'h00);")
    bad=fault/'candidate.v';bad.write_text(changed)
    fault_sources=[sources[0],bad,*sources[2:]]
    with (fault/'build.log').open('w') as f:
        subprocess.run([find_tool('verilator'),'--binary','--timing','-O3','-j','4','-Wno-fatal','--top-module','tb_video',
            '--Mdir',str(fault/'obj'),*map(str,fault_sources)],cwd=fault,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)
    with (fault/'run.log').open('w') as f:
        bad_result=subprocess.run([str(fault/'obj/Vtb_video')],cwd=fault,stdout=f,stderr=subprocess.STDOUT,timeout=180)
    bad_output=(fault/'run.log').read_text()
    rejected=bad_result.returncode!=0 and 'Exact aligned video/request/read-address sample mismatch' in bad_output
    record=json.loads((out/'report.json').read_text())
    record['render_fault']=dict(rejected=rejected,exit_code=bad_result.returncode,source_sha256=digest(bad),
        log_sha256=digest(fault/'run.log'),output=bad_output)
    record['passed']=record['passed'] and rejected
    record['source_identity_verified']=all(digest(Path(p))==h for p,h in protected.items())
    record['passed']=record['passed'] and record['source_identity_verified']
    (out/'report.json').write_text(json.dumps(record,indent=2)+'\n')
    print('Exact sample oracle rejects active-red RTL corruption:',rejected,flush=True)
    print(out/'report.json',flush=True)
    if not record['passed']:raise SystemExit(1)


if __name__=='__main__':main()
